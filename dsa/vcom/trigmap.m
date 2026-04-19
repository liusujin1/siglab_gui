  function newselect = trigmap(select,nio_act_file)
% function newselect = trigmap(select,nio_act_file)
% remap trigger selection given the reality of the hardware state
% Dick Benson, DSP Technology
  nio_actual=nio_act_file(1:2);
  if length(nio_act_file)==2
     nio_file  = [2,2]; % assume 2 in 2 out
  else
     nio_file  = nio_act_file(3:4);
  end;
 
  % first decide where we were pointed...
  if select <=nio_file(1)
     % it was an input channel
     if select <= nio_actual(1)
         % no problem
         newselect=select;
     else
         % beyond the actual channels in this config
         newselect = 1;
         disp('Warning: input trigger channel specified beyond number currently available');
     end;
  elseif select <=nio_file(1)+nio_file(2) +1
     % trigger was an output channel .....  OR EXTERNAL ! note the +1 above added 8/25/99 RAB
     outchan = select-nio_file(1);
     
     if outchan == nio_file(2)+1
           % must be external trigger added 8/25/99 RAB 
           newselect = nio_actual(1)+nio_actual(2)+1; 
     elseif outchan <= nio_actual(2)
           % no problem, remap 
           newselect=outchan+nio_actual(1); 
     else
        % beyond current output current capability
        if nio_actual(2)==0
            newselect=1;
            disp('Warning: output subsystem not present, input channel 1 selected for trigger');
        else
            newselect=nio_actual(1)+1;
            disp('Warning: output trigger specified beyond number currently available');
        end;
     end;
  else
      disp('Error in trigmap.m, selection inconsistant with nio_file');
      disp('Trigger will default to Input Channel 1');
      newselect =1;
      nio_act_file
      select
  end;
% end function








