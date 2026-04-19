  function hcpyv5(Action,In1,In2)
% function hcpyv5(Action,In1,In2)
% Hardcopy support for VI's running under MATLAB 5
% Assumes only 1 VI hdcpy active at a time. 
% Actions
%    init        
%        handle of figure to be printed in In1

% Dick Benson, DSP Technology  

% Fixed eval related stuff to swallow path names with spaces e.g.:  G:\sig lab\vcom   3/21/2k


  global HHCPYV5_; 
  
%define  
  %Fig          = 1;
  %Color_rb     = 2;
  %BW_rb        = 3;
  %Mode_pu      = 4;
  %ClipBd_rb    = 5;
  %File_rb      = 6;
  %Dev_rb       = 7;
  %File_lbl     = 8;
  %Path_File    = 9;
  %Print_pb     = 10;
  %Open_Dev_pb  = 11;
  %Pic_Fig      = 12; % owner figure handle (index)
%end_define


% ****************************************************
% common color definitions
%include
% vcol_h.m                                                   
%%%%%%%%%%%%%%%%%%%%%%%%%end_include
% **************************************************** 

  if strcmp(Action,'init') 
     HHCPYV5_(12)= In1;             % owner figure handle
     ppos           = get(In1,'pos');
     vsize          = 105;               % height of this applet
     ypos=ppos(2)+ppos(4)-vsize;
     
     % added to work around print problem on WGD project 10/27/98
     % needed to select the renderer
     if nargin ==3
        renderer = In2;     
     else
        % renderer = '-zbuffer';
          renderer = '-painters';  % back to painters per Audrey B. @ TMW 12/18/98
     end;
     
     % if the file vi_color.mat with startup colors exists,
     % use the colors in it rather than INIT_COLORc
     if exist('vi_color.mat') ==2
        load vi_color 
        hdcpy_color=stored_vi_colors;
     else
        hdcpy_color=[[0,0,.25098];[0,0.50196,0.50196];[0.75294,0.75294,0.75294];[1,1,0];[0,0,0];[0,0,1];[0,1,0];[0,1,1];[1,1,0];[1,0,0];[0.25098,0,0];[0,1,0];[0.75294,0.75294,0.75294];[1,1,1];[1,1,1];[0,0,0];[0,0,0];[0,0,0];[0,0,0];[1,1,0];[1,0,0];[.3,.3,.3];[0.5, 1, 0];];
     end; 

     HHCPYV5_(1)=figure('numbertitle','off','resize','off','menu','none',...
                        'visible','on',...
                        'pos',[ppos(1),ypos, 360, vsize],...
                        'color',hdcpy_color(2,:),... 
                        'name','Hardcopy','BackingStore','off',...
                        'WindowStyle','Modal'); 

   
     % see if we can restore state from a file
     
     [drive,ppath] = pathfind('vcom'); 
     filen = '\vhdcpy5.mat';
     if exist([drive,ppath,'\vhdcpy5.mat']) >0, 
        eval(['load ''',drive,ppath,filen,''' ']);
        
     else
        % Do a manual job of restoring the two variables in the file here
        % These entries correspond 1:1 with handle indexes
        % These initial states MUST be self consistent or the scheme won't
        % work. e.g you can't have two pushbuttons in the same group on
        % and file edit is not enabled when the clip board is selected.
        %       value  enable
        hdcpy_s1=[ 0     0
                   0     1
                   1     1
                   1     1
                   1     1
                   0     1
                   0     0
                   0     1
                   0     0
                   1     1
                   0     0 ]; 
       hdcpy_s2 = 'none.xxx';  % no file for output spec'd          
     end;         

     uicontrol('style','pushbutton','str','Cancel','pos',[160 5  55 20],...
               'callback','close(gcf)'); 

     HHCPYV5_(10) = uicontrol('style','pushbutton','str','Print',...
                             'pos' ,[100 5  55 20],...
                             'callback',['hcpyv5(''print'')'],...
                             'userdata',renderer);   % added 10/27/98
     

     HHCPYV5_(11) = uicontrol('style','pushbutton','str','File Select',...
                             'pos' ,[10 5  85 20],...
                             'callback',['hcpyv5(''Open_Dev_pbcb'')']);
     
     HHCPYV5_(2)  = uicontrol('style','radio','str','Color',...
                               'pos',[10 85 85 15],...
                               'backgroundcolor',hdcpy_color(3,:),... 
                               'callback','hcpyv5(''rbg1'',''C'')',...
                               'value',hdcpy_s1(2,1));
                  
                  
     HHCPYV5_(3)  = uicontrol('style','radio','str','B and W',...
                               'pos',[10 70 85 15],...
                               'backgroundcolor',hdcpy_color(3,:),...
                               'callback','hcpyv5(''rbg1'',''BW'')',...
                               'value',hdcpy_s1(3,1));
                               

     devlist=['Win Meta|Bit Map|HPGL|LaserJet IIp|Post Script|Encaps PS|Windows'];
     HHCPYV5_(4)  = uicontrol('style','popup','str',devlist,...
                                  'pos',[120 75 100 15],...
                                  'backgroundcolor',hdcpy_color(13,:),...
                                  'foregroundcolor',hdcpy_color(19,:),...
                                  'callback','hcpyv5(''Mode_pucb'')',...
                                  'value',hdcpy_s1(4,1));
 
     HHCPYV5_(5)  = uicontrol('style','radio','str','Clip Board',...
                                    'pos',[245 85 100 15],...
                                    'backgroundcolor',hdcpy_color(3,:),...
                                    'foregroundcolor',hdcpy_color(16,:),...
                                    'callback','hcpyv5(''rbg2'',''cb'')',...
                                    'value',hdcpy_s1(5,1));
                              

     HHCPYV5_(6)  = uicontrol('style','radio','str','File',...
                                  'pos',[245 70 100 15],...
                                  'backgroundcolor',hdcpy_color(3,:),...
                                  'foregroundcolor',hdcpy_color(16,:),...
                                  'callback','hcpyv5(''rbg2'',''file'')',...
                                  'value',hdcpy_s1(6,1));

     HHCPYV5_(7)  = uicontrol('style','radio','str','Device',...
                                 'pos',[245 55 100 15],...
                                 'backgroundcolor',hdcpy_color(3,:),...
                                 'foregroundcolor',hdcpy_color(16,:),...
                                 'callback','hcpyv5(''rbg2'',''dev'')',...
                                 'value',hdcpy_s1(7,1));
                              

     HHCPYV5_(8) = uicontrol('style','text','str','Path/File',...
                                  'pos',[10 50 75 15],...
                                  'backgroundcolor',hdcpy_color(3,:),...
                                  'foregroundcolor',hdcpy_color(16,:),...
                                  'callback','',...
                                  'horiz','left');

     HHCPYV5_(9) = uicontrol('style','text','str',hdcpy_s2,...
                                   'pos',[10 35 335 15],...
                                   'backgroundcolor',hdcpy_color(3,:),...
                                   'foregroundcolor',hdcpy_color(16,:),...
                                   'callback','',... 
                                   'horiz','left');
     
     % do the enabelz skewed by one cause of Fig=1

     for i=2:11 
         if hdcpy_s1(i,2)==1
            set(HHCPYV5_(i),'enable','on'); 
         else
            set(HHCPYV5_(i),'enable','off'); 
         end; 
     end; 
     
     if get(HHCPYV5_(7),'value')==0
         set(HHCPYV5_(11),'str','File Select');
     else
         set(HHCPYV5_(11),'str','Dev Select');
     end; 
                              
     set(HHCPYV5_(1),'visible','on');
     
     % bwval=get(HHCPYV5_(BW_rb),'value')                           
     % text =get(HHCPYV5_(BW_rb),'string') 
     % colval=get(HHCPYV5_(Color_rb),'value')                           
     % text =get(HHCPYV5_(Color_rb),'string')      
     % drawnow;
     % bw/color radio buttons do not init correctly.. 
     % can't duplicate in a 'simple' program

  elseif strcmp(Action,'rbg1')
        if strcmp(In1,'C')
           set(HHCPYV5_(2),'value',1); 
           set(HHCPYV5_(3),'value',0); 
        elseif strcmp(In1,'BW') 
           set(HHCPYV5_(3),'value',1);
           set(HHCPYV5_(2),'value',0); 
        else
           disp('oops');
        end;
        
   elseif strcmp(Action,'rbg2')
        if strcmp(In1,'cb')
           set(HHCPYV5_(5),'value',1);
           set(HHCPYV5_(6),'value',0); 
           set(HHCPYV5_(7),'value',0); 
           set(HHCPYV5_(11),'enable','off');
           set(HHCPYV5_(10),'enable','on');
           set(HHCPYV5_(9),'enable','off'); 
        elseif strcmp(In1,'file') 
           set(HHCPYV5_(5),'value',0);
           set(HHCPYV5_(6),'value',1);
           set(HHCPYV5_(7),'value',0);
           set(HHCPYV5_(11),'str','File Select','enable','on');
           file_n= get(HHCPYV5_(9),'str'); 
           if length(file_n)<5
              file_n='none'; % something wrong
              set(HHCPYV5_(9),'str',file_n);
           end; 
           if strcmp('none',file_n(1:4))
             set(HHCPYV5_(10),'enable','off');
           end;
           set(HHCPYV5_(9),'enable','on');
        elseif strcmp(In1,'dev')
           set(HHCPYV5_(5),'value',0);
           set(HHCPYV5_(6),'value',0);
           set(HHCPYV5_(7),'value',1);
           set(HHCPYV5_(11),'str','Dev Select','enable','on');
           set(HHCPYV5_(10),'enable','on');
           set(HHCPYV5_(9),'enable','off');
        else
           disp('oooops');
        end;
        
   elseif strcmp(Action,'Mode_pucb')                           
      %devlist=['Win Meta|Bit Map|HPGL|LaserJet IIp|Post Script|Encap PS|Windows'];
      sel=get(HHCPYV5_(4),'value');  % the selection
      
      ext=['WMF';'BMP';'HGL';'JET';'PS ';'EPS';'xxx';'xxx']; % note space in PS
      
      file_n=get(HHCPYV5_(9),'str');
     
      
      if sel < 7
         if length(file_n)<5
            file_n='none'; % something wrong 
            set(HHCPYV5_(9),'str',file_n);
         end; 
         lf=length(file_n);
         if strcmp('.',file_n(lf-3))
            % legit extension, replace it 
            set(HHCPYV5_(9),'str',[file_n(1:lf-3),ext(sel,:)]);
         else
            % add to it
            set(HHCPYV5_(9),'str',[file_n,ext(sel,:)]);
         end;
      else
          
      end;

      if sel==1 | sel==2
         % for metafile and bitmap....
      
         % lock out BW for bitmap
         if sel==2
             set(HHCPYV5_(2),'value',1); 
             set(HHCPYV5_(3),'value',0,'enable','off');
         else 
             set(HHCPYV5_(2),'value',1,'enable','on');
             set(HHCPYV5_(3),'value',0,'enable','on');
         end;
       
         % enable clipboard choice , disable device choice
         set(HHCPYV5_(5),'enable','on');
         if get(HHCPYV5_(7),'value')==1
            set(HHCPYV5_(6),'value',1,'enable','on');
            set(HHCPYV5_(7),'value',0);
            set(HHCPYV5_(11),'str','File Select','enable','on');
         end; 
         set(HHCPYV5_(7),'enable','off');
         set(HHCPYV5_(10),'enable','on');
      
      else
         set(HHCPYV5_(3),'enable','on','value',1);
         if sel==4 
             % lock out color for IIp laserjet
             set(HHCPYV5_(2),'value',0,'enable','off'); 
         else
             % unlock color for others 
             set(HHCPYV5_(2),'value',0,'enable','on'); 
         end;

         % for all others, disable clipboard, enable device 
         if get(HHCPYV5_(5),'value')==1
             set(HHCPYV5_(5),'value',0);
             set(HHCPYV5_(6),'value',1);
             set(HHCPYV5_(11),'str','File Select','enable','on');
         end;
         set(HHCPYV5_(5),'enable','off');
         
         if sel < 7
            if sel ==4
               set(HHCPYV5_(7),'value',0,'enable','on');
            else
               % disable 'device' for hpgl, postscript, and eps
               set(HHCPYV5_(7),'value',0,'enable','off');
            end;
            
            set(HHCPYV5_(6),'value',1,'enable','on' );
            file_n= get(HHCPYV5_(9),'str');
            if strcmp('none',file_n(1:4))
               set(HHCPYV5_(10),'enable','off');
            end;
            
         else
            % go to windows printer, no files, no clipboard, just do it... 
            set(HHCPYV5_(7),'value',1,'enable','on');
            set(HHCPYV5_(6),'value',0,'enable','off' );
         end;
         
         
      end;
      if get(HHCPYV5_(6),'value')==1
        set(HHCPYV5_(9),'enable','on');
      else
        set(HHCPYV5_(9),'enable','off');
      end;
  
  
  elseif strcmp(Action,'Open_Dev_pbcb');
      if strcmp(get(HHCPYV5_(11),'str'),'File Select')
         ext=['WMF';'BMP';'HGL';'JET';'PS ';'EPS';'xxx']; % note space in PS
         [file_n,path_n]= uiputfile(['*.',ext(get(HHCPYV5_(4),'value'),:)],'Open (new) File',0.5,0.5);
         if file_n ~=0
             set(HHCPYV5_(9),'str',[path_n,extcheck(ext(get(HHCPYV5_(4),'value'),:),file_n)]);
             set(HHCPYV5_(10),'enable','on'); 
         end;     
      elseif strcmp(get(HHCPYV5_(11),'str'),'Dev Select')
         print -dsetup
      else
         disp('oooops, invalid Open_Dev_pbcb string');
      end; 
   
  elseif strcmp(Action,'print')
     
     invert_str= get(HHCPYV5_(12),'inverthardcopy'); % just in case its on
   %%   set(HHCPYV5_(Pic_Fig),'papertype','A4');
     set(HHCPYV5_(12),'inverthardcopy','off');       % turn it off
     figure(HHCPYV5_(12));                           % change to figure to be plotted
     drawnow;
    
     % system_dependent(Mystery,'on');   % you have got me!! it makes meta-file
                                         % format work with MS-DRAW
     % get the stuff we need b4 closing this figure
     % now a moot point since the close is at the end;
     color_flg  = get(HHCPYV5_(2),'value');    % 1=color 0= quasi B&W
     print_mode = get(HHCPYV5_(4),'value');

     % added explicit figure handle with -f option 2/25/98 (rab)
     print_string=['print -f',int2str(HHCPYV5_(12))]; % start with this and build up
  
     
     %  devlist=['Win Meta|Bit Map|HPGL|LaserJet IIp|Post Script|Encaps PS|win'];
     
     renderer = get(HHCPYV5_(10),'userdata'); % 10/28/98
     
     if color_flg
        options={[' ',renderer,' -dmeta '];' -dbitmap ';' -dhpgl ';' -dljet2p ';' -dpsc ';' -depsc ';' -dwinc '};
     else
        options={[' ',renderer,' -dmeta ']; '-dbitmap ';' -dhpgl ';' -dljet2p ';' -dps ';' -deps  ';' -dwin  '};
     end;
     
     % zbuffer messes up doc->pdf !!!


     % add selected option to the string
     print_string=[print_string,options{print_mode,:},' '];

     if get(HHCPYV5_(6),'value')==1 
        % we are printing to a file
        path_filen = get(HHCPYV5_(9),'string');
        print_string=[print_string,'''',path_filen,''''];  % 3/21/2k
         
     end;
     print_string = [print_string,' -noui']; % 3/21/2k
     
     
     hax = [];
     hax = findobj(HHCPYV5_(12),'type','axes');
     lax = length(hax);
     
     if color_flg==0
        % quasi monochrome (B&W)
        fig_col=get(HHCPYV5_(12),'color');
        
        % save axis colors
        

        ax_kids = [];
      
        ax_col  = zeros(lax,3);
        ax_spcl = zeros(lax,1); % mark 'none' color axes 
        for i=1:lax
            x=   get(hax(i),'color');
            if strcmp(x,'none')
               ax_spcl(i) = 1; % mark it
            else
               ax_col(i,:)  = x;
            end;
            ax_kids      = [ax_kids;get(hax(i),'children')];
            
            
            x_col(i,:)   = get(hax(i),'xcolor');
            y_col(i,:)   = get(hax(i),'ycolor');
            t_col(i,:)   = get(get(hax(i),'title'),'color');
            
            % if border color is same as figure background, 
            % it does not show therefore make it white
            % by setting the ax_spcl flag 
            if x_col(i,:) == fig_col
               ax_spcl(i) = ax_spcl(i) + 2;
            end;
            
        end;
        
        lax_kids = length(ax_kids);
        for i=1:lax_kids 
            if strcmp(get(ax_kids(i),'type'),'patch')
                kid_col(i,:) = get(ax_kids(i),'facecolor');
                set(ax_kids(i),'facecolor',[.25 .25 .25]);
            else
                kid_col(i,:) = get(ax_kids(i),'color');
                set(ax_kids(i),'color',[0 0 0]);
            end;
        end;
     
        set(HHCPYV5_(12),'color',[1,1,1]);
        
        for i= 1:lax
           if ax_spcl(i)==1 |  ax_spcl(i)==3
              % disp('debug: no axis color');
              % a see through
           else
              set(hax(i),'color',[1 1 1]);
           end;
           if ax_spcl(i)>=2
              % axis border color is same as fig, don't show it
              set(hax(i),'xcolor',[1 1 1],'ycolor',[1 1 1] );
           else
              % normally make it black
              set(hax(i),'xcolor',[0 0 0],'ycolor',[0 0 0] );
           end;
        end;
        
        % deal with axis labels .... 
        for i=1:lax
           if get(get(hax(i),'title'),'color')==fig_col
              set(get(hax(i),'title'),'color',[1 1 1]);
           else
              set(get(hax(i),'title'),'color',[0 0 0]);
           end;
           
           if ax_spcl(i) >=2
               % marked as being the same as the fig color 
               % therefore it does not show up .. oh the tricks ... 
               set(get(hax(i),'xlabel'),'color',[1 1 1]);
               set(get(hax(i),'ylabel'),'color',[1 1 1]);
           else
               set(get(hax(i),'xlabel'),'color',[0 0 0]);
               set(get(hax(i),'ylabel'),'color',[0 0 0]);
           end;
        end;
        % drawnow discard
     else
        % leave well enough alone  .... may have to mess with 
        % figure invert hardcopy property ... we will see ... yes coming up
     end;
    
       %  disp(print_string);
       
       set(HHCPYV5_(1),'pointer','watch'); % this actually seems to work in v5!
       drawnow; pause(1);
       
       if print_mode ==2 
                % BitMap Mode... do not mess with pixels vs normalized for this mode
                % God the screwing around on must do....
                set(HHCPYV5_(1),'visible','off'); % seems to get in the way of v5
                % even though the focus should have changed to the target figure
                % drawnow discard;  % didn't cut it
                refresh             % but the old spit nickles did it.
                % the -noui option speeds things up by 10:1 and you still get the 
                % uicontrols .... different things constantly vary
                % eval([print_string,' -noui']);              % do the work: print
                eval(print_string);
                set(HHCPYV5_(1),'visible','on'); % seems to get in the way of v5
                
       else
                drawnow discard;
                eval(print_string);
                
       end;
       % XXXXX


     if color_flg == 0 & (print_mode ~=4  | print_mode ~=7)
        % we were in B&W mode
        % restore all colors to the original values... 
        set(HHCPYV5_(12),'color',fig_col); % restore figure 
        for i=1:lax
           if ~rem(ax_spcl(i),2)
               set(hax(i),'color',ax_col(i,:));   % restore axis
           end;
           set(hax(i),'xcolor',x_col(i,:));
           set(hax(i),'ycolor',y_col(i,:));
           set(get(hax(i),'title'),'color',t_col(i,:));
        end;
        for i=1:lax_kids 
           if strcmp(get(ax_kids(i),'type'),'patch')
               set(ax_kids(i),'facecolor',kid_col(i,:));
           else
               set(ax_kids(i),'color',kid_col(i,:));
           end;
        end; 
        % must do a drawnow if bitmap is used XXXX 4.2 bug
        if print_mode==2 
           drawnow
        else
           drawnow discard
        end;
     else
        % nothing to do for color
     end;

     % retrieve state 
     hdcpy_s1=zeros(11,2);
     for i=2:11 
        hdcpy_s1(i,1)=get(HHCPYV5_(i),'value');
        if strcmp( get(HHCPYV5_(i),'enable') ,'on')
          hdcpy_s1(i,2)=1;
        else
          % already zero
        end; 
     end;
     hdcpy_s2=get(HHCPYV5_(9),'str'); 
     % save state to a file
     filen = '\vhdcpy5.mat';
     [drive,ppath] = pathfind('vcom');
     s1=[' save ''',drive,ppath,filen,''' hdcpy_s1 hdcpy_s2 -v4'];
     eval(s1);

     close(HHCPYV5_(1));                  % close this dialog for the print
     
     % refresh;                             % MATLAB bug
     
     set(HHCPYV5_(12),'inverthardcopy',invert_str); % restore
     clear HHCPYV5_                         % be a good citizen ... 
     
  else % end of  print 
     disp([Action,' oops']);
  end; % main if then else construction (switchyard)
% end hcpyv5 function





